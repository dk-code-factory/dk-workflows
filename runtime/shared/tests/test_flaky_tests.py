"""Issue lifecycle, provenance and failure-coverage acceptance tests (no network)."""
import copy
import io
import json
import multiprocessing
from pathlib import Path
import sys
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared import ci_policy as policy
from shared.flaky_tests import Registry, digest, identity, record_flaky
from shared.flaky_actions import collect_attempt, discover, GitHub, pr_repository
from shared.test_evidence import parse_report
from shared.private_state import mutate, read, root

SHA = 'a' * 40
OTHER = 'b' * 40
REPO = 'github.com/acme/project'
WORKFLOW = '.github/workflows/ci.yml'
JOB = 'tests (linux)'
TEST = 'tests/test_api.py::test_retry'
SIGNATURE = 'AssertionError: response was late'
URL = 'https://github.com/acme/project/issues/7'
FAIL = f'FAILED {TEST} - {SIGNATURE}\n================ 1 failed in 0.10s ================'
PASS = f'{TEST} PASSED [100%]\n================ 1 passed in 0.10s ================'
CONTEXT = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)


def declaration(explicit=True, **changes):
    return '```dk-flaky\n' + json.dumps(CONTEXT | {'flaky': explicit} | changes) + '\n```'


def archive(text, steps=None):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w') as bundle:
        for number, value in (steps or {1: text}).items():
            member = zipfile.ZipInfo(f'{JOB}/{number}_Tests.txt', date_time=(2026, 1, 1, 0, 0, 0))
            bundle.writestr(member, value)
    return stream.getvalue()


class FakeAPI:
    def __init__(self, text=FAIL, body=None, head=SHA, attempts=1):
        self.repo = REPO
        self.issue_data = {'number': 7, 'html_url': URL, 'state': 'open',
                           'title': 'test flake', 'body': declaration() if body is None else body,
                           'updated_at': '2026-10-01T00:00:00Z'}
        self.run = {'id': 10, 'run_attempt': attempts, 'head_sha': head, 'status': 'completed',
                    'path': WORKFLOW, 'conclusion': 'failure'}
        self.texts = {i: text for i in range(1, attempts + 1)}
        self.fail_issue = False
        self.calls = []
        self.job_changes = {}

    def issue(self, number):
        self.calls.append(('issue', number))
        if self.fail_issue:
            raise RuntimeError('inaccessible issue')
        return copy.deepcopy(self.issue_data)

    def open_issues(self, limit):
        self.calls.append(('issues', limit))
        return [copy.deepcopy(self.issue_data)] if self.issue_data['state'] == 'open' else []

    def jobs(self, run_id, attempt):
        self.calls.append(('jobs', run_id, attempt))
        outcome = 'success' if 'PASSED' in self.texts[attempt] else 'failure'
        return [{'id': 100 + attempt, 'run_id': run_id, 'head_sha': self.run['head_sha'],
                 'run_attempt': attempt, 'name': JOB, 'status': 'completed', 'conclusion': outcome,
                 'html_url': f'https://github.com/acme/project/actions/runs/10/job/{100+attempt}',
                 'completed_at': f'2026-10-01T00:0{attempt}:00Z',
                 'steps': [{'number': 1, 'name': 'Tests', 'status': 'completed', 'conclusion': outcome,
                            'completed_at': f'2026-10-01T00:0{attempt}:00Z'}], **self.job_changes}]

    def request(self, endpoint, binary=False):
        self.calls.append(('request', endpoint))
        if endpoint.startswith('actions/runs?'):
            return {'total_count': 1, 'workflow_runs': [copy.deepcopy(self.run)]}
        if endpoint == 'actions/runs/10':
            return copy.deepcopy(self.run)
        if endpoint.startswith('actions/runs/10/attempts/'):
            attempt = int(endpoint.split('/')[4])
            if endpoint.endswith('/logs'):
                return archive(self.texts[attempt])
            return copy.deepcopy(self.run) | {'run_attempt': attempt}
        raise AssertionError(endpoint)


def snapshot(head=SHA, job_id=101):
    return {'result': 'failure', 'head': head, 'checks': [{'name': JOB, 'workflow': 'CI', 'bucket': 'fail',
            'link': f'https://github.com/acme/project/actions/runs/10/job/{job_id}'}]}


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    return Registry(REPO, tmp_path / 'checkout')


def decide(api, registry, snap=None, auto=True):
    snap = snap or snapshot()
    return policy.decide(api, registry, snap, lambda: copy.deepcopy(snap), auto)


def test_end_to_end_discover_accept_close_reject_reopen(registry):
    api = FakeAPI()
    first = decide(api, registry)
    assert first['result'] == 'known_flakes' and first['raw_result'] == 'failure'
    assert first['ignored_failures'][0]['issues'] == [URL]
    assert Path(first['decision_file']).is_file()
    api.issue_data['state'] = 'closed'
    closed = decide(api, registry)
    assert closed['result'] == 'failure'
    assert closed['blockers'][0]['reason'] == 'test failure has no verified open issue'
    assert len(registry.load()['entries']) == 1
    api.issue_data['state'] = 'open'
    assert decide(api, registry)['result'] == 'known_flakes'
    entry = next(iter(registry.load()['entries'].values()))
    assert [event['status'] for event in entry['history']] == ['active', 'closed', 'active']


def test_inaccessible_issue_never_authorizes(registry):
    api = FakeAPI()
    assert decide(api, registry)['result'] == 'known_flakes'
    api.fail_issue = True
    assert decide(api, registry)['result'] == 'failure'
    assert next(iter(registry.load()['entries'].values()))['status'] == 'unavailable'


def test_explicit_and_historical_proof_and_cross_commit(registry):
    api = FakeAPI(body=declaration(False), attempts=2)
    api.texts[2] = PASS
    report = discover(api, registry)
    assert report['attempts'] == 2
    entry = report['verification']['entries'][0]
    assert entry['active'] and entry['proof']['kind'] == 'same_commit_fail_pass'
    assert entry['proof']['failure']['attempt'] == 1 and entry['proof']['pass']['attempt'] == 2
    observations = registry.load()['observations']
    observations[-1]['head'] = OTHER
    registry.update(lambda data: data.update(observations=observations))
    assert not registry.synchronize([], api.issue)['entries'][0]['active']


@pytest.mark.parametrize('text', ['================ 1 passed in 0.10s ================',
                                 'job finished successfully',
                                 f'{TEST} SKIPPED\n================ 1 skipped in 0.10s ================'])
def test_successful_job_or_skip_is_not_named_pass(registry, text):
    api = FakeAPI(body=declaration(False), attempts=2)
    api.texts[2] = text
    report = discover(api, registry)
    assert not report['verification']['entries'][0]['active']


def test_unrelated_issue_and_changed_signature(registry):
    api = FakeAPI(body=declaration(test='unrelated'))
    assert decide(api, registry)['result'] == 'failure'
    assert not registry.load()['entries']
    api.issue_data['body'] = declaration()
    discover(api, registry)
    api.texts[1] = FAIL.replace(SIGNATURE, 'AssertionError: different failure')
    assert decide(api, registry)['result'] == 'failure'


def test_mixed_known_unknown_test_and_non_test_step(registry):
    api = FakeAPI()
    assert decide(api, registry)['result'] == 'known_flakes'
    api.texts[1] = FAIL.replace('================ 1 failed',
        'FAILED tests/test_api.py::test_other - ValueError: broken\n================ 2 failed')
    mixed = decide(api, registry)
    assert mixed['result'] == 'failure' and len(mixed['ignored_failures']) == 1
    api.texts[1] = FAIL
    old_jobs = api.jobs
    def jobs(run, attempt):
        data = old_jobs(run, attempt)
        data[0]['steps'].append({'number': 2, 'name': 'Deploy', 'status': 'completed', 'conclusion': 'failure'})
        return data
    api.jobs = jobs
    assert decide(api, registry)['result'] == 'failure'


@pytest.mark.parametrize('bucket', ['pending', 'cancel'])
def test_unsettled_and_cancelled_checks_block(registry, bucket):
    api = FakeAPI()
    decide(api, registry)
    snap = snapshot()
    snap['checks'].append({'bucket': bucket, 'name': 'build'})
    assert decide(api, registry, snap)['result'] == 'failure'


@pytest.mark.parametrize('text', ['', 'truncated output', 'error: compiler failed', FAIL + '\nError: deployment failed'])
def test_missing_unsupported_and_extra_errors_block(registry, text):
    api = FakeAPI()
    decide(api, registry)
    api.texts[1] = text
    assert decide(api, registry)['result'] == 'failure'


def test_changed_head_old_attempt_and_wrong_job_provenance(registry):
    api = FakeAPI()
    decide(api, registry)
    api.run['head_sha'] = OTHER
    assert decide(api, registry)['result'] == 'failure'
    api.run['head_sha'] = SHA
    api.run['run_attempt'] = 2
    api.texts[2] = FAIL
    assert decide(api, registry)['result'] == 'failure'  # old job link
    assert decide(api, registry, snapshot(job_id=102))['result'] == 'known_flakes'
    api.job_changes['run_attempt'] = 1
    assert decide(api, registry, snapshot(job_id=102))['result'] == 'failure'


def test_rerun_or_snapshot_changes_during_decision_block(registry):
    api = FakeAPI()
    decide(api, registry)
    original = api.request
    def request(endpoint, binary=False):
        response = original(endpoint, binary)
        if endpoint == 'actions/runs/10' and len([c for c in api.calls if c == ('request', endpoint)]) > 1:
            response['run_attempt'] = 2
        return response
    api.calls = []
    api.request = request
    assert decide(api, registry)['result'] == 'failure'
    api.request = original
    changed = snapshot(OTHER)
    changed['result'] = 'head_changed'
    assert policy.decide(api, registry, snapshot(), lambda: changed)['result'] == 'head_changed'


def test_discovery_dedup_same_head_failure_across_attempts(registry):
    api = FakeAPI(body='unrelated')
    decide(api, registry)
    decide(api, registry)
    assert len([c for c in api.calls if c[0] == 'issues']) == 1
    api.run['run_attempt'] = 2
    api.texts[2] = FAIL
    decide(api, registry, snapshot(job_id=102))
    assert len([c for c in api.calls if c[0] == 'issues']) == 1
    api.texts[2] = FAIL.replace(SIGNATURE, 'new signature')
    decide(api, registry, snapshot(job_id=102))
    assert len([c for c in api.calls if c[0] == 'issues']) == 2
    assert len(registry.load()['scans']) == 2


def test_legacy_history_preserved_but_not_active(registry):
    def legacy(data):
        record_flaky(data, TEST, SIGNATURE, SHA, 'fail', 'run1', 'https://example.test/1')
        return record_flaky(data, TEST, SIGNATURE, SHA, 'pass', 'run2', 'https://example.test/2')
    assert registry.update(legacy)['flaky']
    api = FakeAPI(body=declaration(False))
    assert decide(api, registry)['result'] == 'failure'
    assert registry.load()['tests']


def test_repository_normalization_isolation_and_outside_checkout(registry, tmp_path):
    assert Registry('GitHub.com/ACME/Project').path == registry.path
    for repo in ('github.com/fork/project', 'ghe.example/acme/project'):
        assert Registry(repo).path != registry.path and not Registry(repo).load()['observations']
    for bad in ('github.com/../project', '../acme/project', 'owner/repo'):
        with pytest.raises(ValueError):
            Registry(bad)
    with pytest.raises(ValueError, match='outside'):
        Registry(REPO, tmp_path)
    assert pr_repository('https://github.com/acme/project/pull/1') == REPO
    with pytest.raises(ValueError):
        pr_repository('https://github.com/acme/project/pull/1', 'fork/project')


def _add_event(path, number):
    mutate(Path(path), {}, lambda data: data.setdefault('items', []).append(number))


def test_concurrent_atomic_writes(registry):
    processes = [multiprocessing.Process(target=_add_event, args=(str(registry.path), i)) for i in range(12)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(10)
        assert process.exitcode == 0
    assert sorted(registry.load()['items']) == list(range(12))
    assert registry.path.stat().st_mode & 0o777 == 0o600


def test_scan_limits_and_linked_issue_refresh_outside_scan(registry):
    api = FakeAPI()
    decide(api, registry)
    api.open_issues = lambda limit: []
    api.issue_data['state'] = 'closed'
    report = discover(api, registry, max_runs=1)
    assert report['limits_reached'] == ['runs']
    assert report['limits']['days'] == 30
    assert report['verification']['entries'][0]['status'] == 'closed'


def test_report_formats_and_incomplete_evidence():
    junit = '<testsuite tests="2" failures="1" errors="0"><testcase classname="api" name="a"><failure message="late">details</failure></testcase><testcase classname="api" name="b"/></testsuite>'
    parsed = parse_report(junit)
    assert parsed['complete'] and parsed['tests'][1]['outcome'] == 'pass'
    assert not parse_report(junit.replace('tests="2"', 'tests="3"'))['complete']
    assert parse_report(FAIL)['complete']
    assert parse_report(PASS)['tests'][0]['test'] == TEST
    assert not parse_report(FAIL.replace('1 failed', '2 failed'))['complete']
    assert not parse_report(FAIL.replace(SIGNATURE, 'AssertionError: ...'))['complete']
    go = [{'Action': 'run', 'Package': 'p', 'Test': 'TestA'},
          {'Action': 'output', 'Package': 'p', 'Test': 'TestA', 'Output': '    a.go:12: late\n'},
          {'Action': 'fail', 'Package': 'p', 'Test': 'TestA'}, {'Action': 'fail', 'Package': 'p'}]
    assert parse_report('\n'.join(map(json.dumps, go)))['tests'][0]['signature'] == 'a.go:12: late'
    assert not parse_report('\n'.join(map(json.dumps, go[:-1])))['complete']
    assert not parse_report(json.dumps({'Action': 'fail', 'Package': 'p'}))['complete']
    jest = {'numTotalTestSuites': 1, 'numTotalTests': 1, 'numFailedTests': 1, 'numPassedTests': 0,
            'wasInterrupted': False, 'testResults': [{'name': 'a.test.js', 'assertionResults': [
                {'fullName': 'suite retries', 'status': 'failed', 'failureMessages': ['late']}]}]}
    assert parse_report(json.dumps(jest))['tests'][0]['test'] == 'a.test.js::suite retries'
    jest['wasInterrupted'] = True
    assert not parse_report(json.dumps(jest))['complete']


def test_other_provider_and_unknown_outcomes_block(registry):
    api = FakeAPI()
    snap = snapshot()
    snap['checks'][0]['link'] = 'https://other.example/job/1'
    assert decide(api, registry, snap)['result'] == 'failure'
    for raw in ('timeout', 'no_checks', 'head_changed', 'pending'):
        decision = policy.evaluate({'result': raw}, {}, {}, REPO)
        assert decision['result'] == raw and decision['blockers']


def test_linked_issue_is_refreshed_when_open_scan_and_runs_unavailable(registry):
    api = FakeAPI()
    decide(api, registry)
    def unavailable(*a, **k):
        raise RuntimeError('scan unavailable')
    api.open_issues = unavailable
    api.request = unavailable
    api.issue_data['state'] = 'closed'
    report = discover(api, registry)
    assert len(report['missing']) == 2
    assert report['verification']['entries'][0]['status'] == 'closed'


def test_case_normalized_legacy_import_and_cli_roundtrip(registry, tmp_path):
    import os
    import subprocess
    old = root('GitHub.com/Acme/Project') / 'flaky-tests.json'
    mutate(old, {}, lambda data: record_flaky(data, TEST, SIGNATURE, SHA, 'fail', 'legacy', 'https://example.test/legacy'))
    restored = Registry(REPO)
    assert restored.load()['tests']
    script = Path(__file__).resolve().parents[2] / 'dk-pr-iter/scripts/run_state.py'
    args = [sys.executable, str(script), '--repo', REPO, 'flaky', '--test', TEST, '--signature', SIGNATURE,
            '--head', SHA, '--outcome', 'pass', '--attempt', 'new', '--url', 'https://example.test/new']
    completed = subprocess.run(args, capture_output=True, text=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['flaky']
    show = subprocess.run([sys.executable, str(script), '--repo', REPO, 'flaky-show'], capture_output=True, text=True)
    assert len(next(iter(json.loads(show.stdout)['tests'].values()))) == 2
    assert old.is_file()  # history is retained


def test_prose_proof_requires_exact_context_and_unambiguous_claim(registry):
    api = FakeAPI(body='Confirmed flaky test: ' + ' '.join(f'`{v}`' for v in identity(CONTEXT)))
    assert decide(api, registry)['result'] == 'known_flakes'
    api.issue_data['body'] = 'Maybe this is flaky? ' + ' '.join(f'`{v}`' for v in identity(CONTEXT))
    assert decide(api, registry)['result'] == 'failure'
    api.issue_data['body'] = 'This is not flaky. ' + ' '.join(f'`{v}`' for v in identity(CONTEXT))
    assert decide(api, registry)['result'] == 'failure'


def test_success_race_and_missing_checks_cannot_authorize_completion(registry):
    api = FakeAPI()
    green = {'result': 'success', 'head': SHA, 'checks': [{'bucket': 'pass', 'name': 'test'}]}
    red = green | {'result': 'failure', 'checks': [{'bucket': 'fail', 'name': 'test'}]}
    assert policy.decide(api, registry, green, lambda: red)['result'] == 'failure'
    assert policy.evaluate({'result': 'success', 'checks': []}, {}, {}, REPO)['result'] == 'failure'


def test_attempt_api_pagination_and_no_cross_attempt_fallback():
    api = GitHub(REPO)
    calls = []
    def request(endpoint):
        calls.append(endpoint)
        if 'page=2' in endpoint:
            return {'total_count': 101, 'jobs': [{'id': 101}]}
        return {'total_count': 101, 'jobs': [{'id': i} for i in range(1, 101)]}
    api.request = request
    assert len(api.jobs(10, 3)) == 101
    assert all('/attempts/3/jobs?' in endpoint for endpoint in calls)


def test_junit_parent_totals_and_missing_counts_do_not_hide_truncation():
    report = '<testsuites tests="2" failures="1"><testsuite tests="1" failures="1"><testcase name="a"><failure message="late"/></testcase></testsuite></testsuites>'
    assert not parse_report(report)['complete']
    assert not parse_report('<testsuite><testcase name="a"/></testsuite>')['complete']


def test_actions_command_preamble_requires_one_test_command():
    from shared.flaky_actions import archive_report
    step = {'number': 1}
    def report(command):
        text = f'##[group]Run {command}\n{command}\nshell: /usr/bin/bash -e {{0}}\n##[endgroup]\n{FAIL}\n##[error]Process completed with exit code 1.'
        return archive_report(archive(text), JOB, step, [JOB])
    assert report('pytest -v')['complete']
    assert not report('pytest -v; deploy')['complete']
    assert not report('pytest -v\ndeploy')['complete']
    assert not report('arbitrary-wrapper')['complete']
    text = '2026-10-01T00:00:00.1234567Z ' + FAIL.replace('\n', '\n2026-10-01T00:00:00.1234567Z ')
    assert archive_report(archive(text), JOB, step, [JOB])['complete']
    assert not archive_report(archive(FAIL), JOB, step, [JOB, JOB])['complete']


def test_every_failed_step_requires_coverage_and_complete_metadata(registry):
    api = FakeAPI()
    decide(api, registry)
    from shared.flaky_actions import collect_snapshot
    snap = snapshot()
    evidence = collect_snapshot(api, registry, snap)
    verification = registry.synchronize([], api.issue)
    base = policy.evaluate(snap, evidence, verification, REPO)
    assert base['result'] == 'known_flakes'
    for replacement in [[], [{'number': 1, 'conclusion': 'failure', 'status': 'completed',
                               'report': {'complete': True, 'tests': []}}],
                        [{'number': 1, 'conclusion': 'cancelled', 'status': 'completed'}]]:
        changed = copy.deepcopy(evidence)
        changed['checks'][0]['steps'] = replacement
        assert policy.evaluate(snap, changed, verification, REPO)['result'] == 'failure'
    stale = copy.deepcopy(verification)
    stale['entries'][0]['checked_at'] = 'old'
    assert policy.evaluate(snap, evidence, stale, REPO)['result'] == 'failure'


def test_complete_source_evidence_and_issue_lifecycle_retained(registry):
    api = FakeAPI()
    decision = decide(api, registry)
    saved = registry.load()
    entry = next(iter(saved['entries'].values()))
    assert saved['issue_snapshots'][entry['issue_evidence']]['body'] == declaration()
    observation = saved['observations'][0]
    assert Path(observation['source_file']).exists() and Path(observation['archive_file']).exists()
    with zipfile.ZipFile(observation['archive_file']) as bundle:
        assert bundle.read(observation['archive_member']).decode() == FAIL
    # Identical scans retain one observation per attempt/test and one lifecycle event.
    decide(api, registry)
    assert len(registry.load()['observations']) == 1
    assert len(next(iter(registry.load()['entries'].values()))['history']) == 1


def test_jest_complete_text_and_actions_json_exit_trailer():
    text = '''FAIL tests/retry.test.js
  retry
    ✕ responds in time (4 ms)

  ● retry › responds in time

    Expected: 200
    Received: 504

Test Suites: 1 failed, 1 total
Tests:       1 failed, 1 total
Snapshots:   0 total
Time:        0.4 s
Ran all test suites.
##[error]Process completed with exit code 1.'''
    report = parse_report(text)
    assert report['complete'] and report['tests'][0]['test'] == 'tests/retry.test.js::retry responds in time'
    assert not parse_report(text.replace('1 failed, 1 total', '2 failed, 2 total'))['complete']
    assert not parse_report(text.replace('Ran all test suites.', ''))['complete']
    go = [{'Action': 'output', 'Package': 'p', 'Test': 'T', 'Output': 'late'},
          {'Action': 'fail', 'Package': 'p', 'Test': 'T'}, {'Action': 'fail', 'Package': 'p'}]
    log = '\n'.join(map(json.dumps, go)) + '\n##[error]Process completed with exit code 1.'
    assert parse_report(log)['complete']


def test_partial_issue_context_remains_candidate_despite_history(registry):
    api = FakeAPI(body=f'Flaky test `{TEST}` with failure `{SIGNATURE}`', attempts=2)
    api.texts[2] = PASS
    report = discover(api, registry)
    assert report['verification']['entries'][0]['status'] == 'candidate'
    assert report['verification']['entries'][0]['issue_url'] == URL
    assert not report['verification']['entries'][0]['active']
