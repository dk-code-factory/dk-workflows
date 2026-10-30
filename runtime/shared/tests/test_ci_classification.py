"""Bound automatic CI classification without weakening live acceptance gates."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from shared import ci_policy as policy, flaky_actions as actions
from shared.ci_deadline import Budget, BoundedAPI, ClassificationTimeout
from shared.flaky_tests import Registry
from test_flaky_tests import FakeAPI, REPO, WORKFLOW, JOB, TEST, SIGNATURE, FAIL, PASS, snapshot, declaration


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    return Registry(REPO)


def decide(api, registry, **kwargs):
    snap = snapshot()
    return policy.decide(api, registry, snap, lambda: copy.deepcopy(snap), **kwargs)


@pytest.mark.parametrize('text', ['compiler failed', '', 'truncated report',
                                 FAIL + '\nError: deployment failed'])
def test_unparseable_current_failure_never_scans_history(registry, text):
    api = FakeAPI(text=text)
    result = decide(api, registry)
    assert result['result'] == result['raw_result'] == 'failure'
    assert result['blockers']
    assert not any(call[0] == 'issues' or call[0] == 'request' and '?' in call[1]
                   for call in api.calls)
    assert not registry.load()['scans']


def test_mixed_parser_and_unknown_failures_do_not_scan(registry):
    api = FakeAPI(body='unrelated')
    jobs = api.jobs
    def mixed(run, attempt):
        result = jobs(run, attempt)
        result[0]['steps'].append({'number': 2, 'name': 'Build', 'status': 'completed',
                                   'conclusion': 'failure'})
        return result
    api.jobs = mixed
    result = decide(api, registry)
    assert result['result'] == 'failure'
    assert not any(call[0] == 'issues' for call in api.calls)


def test_explicit_issue_proof_avoids_all_historical_archives(registry):
    api = FakeAPI()
    progress = []
    result = decide(api, registry, on_progress=progress.append)
    assert result['result'] == 'known_flakes'
    assert sum(call == ('request', 'actions/runs/10/attempts/1/logs') for call in api.calls) == 1
    assert not any(call[0] == 'request' and call[1].startswith('actions/runs?') for call in api.calls)
    assert progress[0]['raw_result'] == 'failure'
    assert progress[0]['phase'] == 'classification'


def test_targeted_discovery_skips_unrelated_workflows_and_reuses_attempts(registry):
    api = FakeAPI(body=declaration(False), attempts=2)
    api.texts[2] = PASS
    original = api.request
    def request(endpoint, binary=False):
        result = original(endpoint, binary)
        if endpoint.startswith('actions/runs?'):
            result['workflow_runs'].insert(0, dict(api.run, id=999, path='.github/workflows/unrelated.yml'))
        return result
    api.request = request
    target = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)
    first = actions.discover(api, registry, targets=[target])
    assert first['attempts'] == 2
    count = len([call for call in api.calls if call[0] == 'request' and call[1].endswith('/logs')])
    api.issue_data['body'] = 'unrelated'
    second = actions.discover(api, registry, targets=[target])
    assert second['cached_attempts'] == 2
    assert len([call for call in api.calls if call[0] == 'request' and call[1].endswith('/logs')]) == count
    assert not any('999/' in call[1] for call in api.calls if call[0] == 'request')
    api.issue_data['body'] = declaration(False)
    assert actions.discover(api, registry, targets=[target])['verification']['entries'][0]['active']
    api.issue_data['state'] = 'closed'
    assert not actions.discover(api, registry, targets=[target])['verification']['entries'][0]['active']


def test_deadline_during_current_collection_is_retained_and_never_accepts(registry):
    api = FakeAPI()
    tick = [0]
    original = api.request
    def request(*args, **kwargs):
        tick[0] += 2
        return original(*args, **kwargs)
    api.request = request
    progress = []
    result = decide(api, registry, classification_timeout=3, clock=lambda: tick[0],
                    on_progress=progress.append)
    assert result['result'] == 'failure' and result['raw_result'] == 'failure'
    assert result['classification']['timed_out'] is True
    assert any('deadline' in item['reason'] for item in result['blockers'])
    assert Path(result['decision_file']).is_file()
    assert len(api.calls) == 2
    assert progress[0]['raw_result'] == 'failure'


def test_deadline_during_discovery_retains_failed_scan_and_cannot_accept(registry):
    api = FakeAPI(body=declaration(False))
    tick = [0]
    original = api.open_issues
    def issues(limit):
        tick[0] = 100
        return original(limit)
    api.open_issues = issues
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['result'] == 'failure' and result['classification']['timed_out']
    scan = next(iter(registry.load()['scans'].values()))
    assert scan['status'] == 'timed_out'
    assert not any(call[0] == 'request' and '?' in call[1] for call in api.calls)


def test_deadline_after_live_refresh_revokes_known_flake_acceptance(registry):
    api = FakeAPI()
    assert decide(api, registry)['result'] == 'known_flakes'
    tick = [0]
    def refresh():
        tick[0] = 10
        return snapshot()
    result = policy.decide(api, registry, snapshot(), refresh, classification_timeout=5,
                           clock=lambda: tick[0])
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['ignored_failures'] == []


def test_real_transport_caps_requests_retries_and_issue_scan_to_remaining_budget(monkeypatch):
    tick = [0]
    calls = []
    def run(argv, **kwargs):
        calls.append(kwargs['timeout'])
        tick[0] += 1
        return subprocess.CompletedProcess(argv, 0, stdout='[]' if kwargs.get('text') else b'{}', stderr='')
    monkeypatch.setattr(subprocess, 'run', run)
    api = BoundedAPI(actions.GitHub(REPO), Budget(3, clock=lambda: tick[0]))
    api.request('actions/runs/10')
    api.request('actions/runs/10')
    with pytest.raises(ClassificationTimeout):
        api.open_issues(1)
    assert calls == pytest.approx([3, 2, 1], abs=0.01)
    with pytest.raises(ClassificationTimeout):
        api.request('actions/runs/10')
    assert len(calls) == 3


@pytest.mark.parametrize('seconds', [0, -1, float('nan'), float('inf')])
def test_invalid_classification_budget_never_dispatches(registry, seconds):
    api = FakeAPI()
    with pytest.raises(ValueError, match='positive and finite'):
        decide(api, registry, classification_timeout=seconds)
    assert not api.calls


def test_final_snapshot_reads_share_the_remaining_deadline_and_restore_context(registry, monkeypatch):
    from shared import github_reads
    from shared.ci_deadline import ACTIVE_BUDGET
    tick = [0]
    calls = []
    def run(argv, **kwargs):
        calls.append(kwargs['timeout'])
        tick[0] = 10
        raise subprocess.TimeoutExpired(argv, kwargs['timeout'])
    monkeypatch.setattr(subprocess, 'run', run)
    def refresh():
        tick[0] = 4
        return github_reads.run(['gh', 'pr', 'view', '1'], capture_output=True)
    result = policy.decide(FakeAPI(), registry, snapshot(), refresh,
                          classification_timeout=5, clock=lambda: tick[0])
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert calls == pytest.approx([1], abs=0.01)
    assert ACTIVE_BUDGET.get() is None


def test_corrupt_cached_archive_is_recollected(registry):
    api = FakeAPI()
    actions.collect_attempt(api, registry, api.run, 1)
    evidence = actions.collect_attempt(api, registry, api.run, 1, reuse=True)
    assert evidence['cached']
    Path(evidence['archive_file']).write_bytes(b'corrupt')
    count = len(api.calls)
    recovered = actions.collect_attempt(api, registry, api.run, 1, reuse=True)
    assert not recovered.get('cached')
    assert Path(recovered['archive_file']).read_bytes() != b'corrupt'
    assert any(call == ('request', 'actions/runs/10/attempts/1/logs') for call in api.calls[count:])


def test_deadline_during_discovery_issue_refresh_records_actual_phase(registry):
    api = FakeAPI(body=declaration(False))
    tick = [0]
    original = api.issue
    issue_reads = [0]
    def issue(number):
        issue_reads[0] += 1
        if issue_reads[0] == 2:
            tick[0] = 10
        return original(number)
    api.issue = issue
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out'] is True
    assert result['classification']['phase'] == 'discover_unknown'
    assert next(iter(registry.load()['scans'].values()))['status'] == 'timed_out'


def test_exact_job_targeting_excludes_other_jobs_and_avoids_unneeded_archive(registry):
    import io
    import zipfile
    api = FakeAPI(body=declaration(False))
    original_jobs = api.jobs
    original_request = api.request
    def jobs(run_id, attempt):
        target = original_jobs(run_id, attempt)[0]
        return [target, dict(target, id=999, name='other job')]
    def request(endpoint, binary=False):
        if endpoint.endswith('/logs'):
            api.calls.append(('request', endpoint))
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as bundle:
                bundle.writestr(f'{JOB}/1_Tests.txt', FAIL)
                bundle.writestr('other job/1_Tests.txt', FAIL.replace(TEST, 'tests/test_other.py::test_other'))
            return stream.getvalue()
        return original_request(endpoint, binary)
    api.jobs, api.request = jobs, request
    target = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)
    class OneTargetPass(list):
        def __iter__(self):
            assert not getattr(self, 'visited', False), 'targets were rescanned per job'
            self.visited = True
            return super().__iter__()
    collected = actions.collect_attempt(api, registry, api.run, 1,
                                        targets=OneTargetPass([target] * 50000))
    assert [item['job'] for item in collected['jobs']] == [JOB]
    assert {item['job'] for item in registry.load()['observations']} == {JOB}
    count = sum(call[0] == 'request' and call[1].endswith('/logs') for call in api.calls)
    absent = actions.collect_attempt(api, registry, api.run, 1,
                                    targets=[dict(target, job='absent job')])
    assert absent['jobs'] == [] and 'archive_file' not in absent
    assert sum(call[0] == 'request' and call[1].endswith('/logs') for call in api.calls) == count


def test_historical_cache_reuses_job_evidence_for_different_unknown_tests(registry):
    api = FakeAPI()
    first = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)
    actions.collect_attempt(api, registry, api.run, 1, targets=[first])
    count = len(api.calls)
    second = dict(first, test='tests/test_other.py::test_other', signature='different error')
    reused = actions.collect_attempt(api, registry, api.run, 1, reuse=True, targets=[second, first])
    assert reused['cached']
    assert len(registry.load()['attempt_evidence']) == 1
    assert not any(call[0] == 'request' and call[1].endswith('/logs') for call in api.calls[count:])


def test_transient_retry_and_backoff_consume_active_classification_budget(monkeypatch):
    from shared import github_reads
    tick = [0.0]
    calls = []
    def run(argv, **kwargs):
        calls.append(kwargs['timeout'])
        tick[0] += 1 if len(calls) == 1 else 2
        return subprocess.CompletedProcess(argv, 1, stdout='', stderr='HTTP 503 unavailable')
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(github_reads.time, 'monotonic', lambda: tick[0])
    monkeypatch.setattr(github_reads.time, 'sleep', lambda seconds: tick.__setitem__(0, tick[0] + seconds))
    monkeypatch.setattr(github_reads.random, 'uniform', lambda *args: 0)
    budget = Budget(3, clock=lambda: tick[0])
    with budget.activate(), pytest.raises(ClassificationTimeout):
        github_reads.run(['gh', 'api', 'repos/acme/project/actions/runs/10'], capture_output=True, text=True)
    assert calls == pytest.approx([3, 1.75])


def test_initial_expiry_retains_blocking_decision(registry):
    calls = [0]
    def clock():
        calls[0] += 1
        return 0 if calls[0] == 1 else 2
    result = decide(FakeAPI(), registry, classification_timeout=1, clock=clock)
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['classification']['phase'] == 'classification'
    assert result['blockers'][-1]['reason'].count('CI classification deadline exceeded') == 1
    assert Path(result['decision_file']).is_file()


def test_scan_allocation_expiry_records_terminal_status(registry, monkeypatch):
    tick = [0]
    original = registry.update
    def update(operation):
        result = original(operation)
        if result is True:  # The scan claim alone returns this sentinel.
            tick[0] = 10
        return result
    monkeypatch.setattr(registry, 'update', update)
    result = decide(FakeAPI(body='unrelated'), registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out']
    scan = next(iter(registry.load()['scans'].values()))
    assert scan['status'] == 'timed_out' and scan['finished_at']


def large_failure_evidence(count):
    tests = [dict(test=f'test_{i}', signature='late', outcome='fail') for i in range(count)]
    evidence = {'checks': [{'link': 'job', 'workflow': WORKFLOW, 'job': JOB,
                           'steps': [{'number': 1, 'report': {'tests': tests}}]}]}
    blockers = [{'link': 'job', 'step': 1, 'test': item['test'], 'signature': item['signature']}
                for item in tests]
    return evidence, blockers


def test_large_report_target_mapping_uses_one_blocker_pass():
    class OnePass(list):
        def __iter__(self):
            assert not getattr(self, 'visited', False), 'repeated blocker scans are quadratic'
            self.visited = True
            return super().__iter__()
    evidence, blockers = large_failure_evidence(50000)
    assert len(policy.discovery_targets(evidence, OnePass(blockers), Budget(60))) == 50000


def test_large_target_mapping_checks_the_deadline_in_batches():
    evidence, blockers = large_failure_evidence(50000)
    tick = [0]
    def clock():
        tick[0] += 1
        return tick[0]
    with pytest.raises(ClassificationTimeout):
        policy.discovery_targets(evidence, blockers, Budget(3, clock=clock))
    assert tick[0] == 4


@pytest.mark.parametrize('auto,claimed', [(False, False), (True, True)])
def test_disabled_or_claimed_discovery_skips_target_mapping(registry, monkeypatch, auto, claimed):
    api = FakeAPI(body='unrelated')
    if claimed:
        decide(api, registry)
    def unexpected(*args):
        raise AssertionError('target reconstruction was unnecessary')
    monkeypatch.setattr(policy, 'discovery_targets', unexpected)
    assert decide(api, registry, auto_discover=auto)['result'] == 'failure'


def test_contended_registry_lock_obeys_active_classification_deadline(registry):
    import fcntl
    lock_path = registry.path.parent / (registry.path.name + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    script = '''
from shared.flaky_tests import Registry
from shared.ci_deadline import Budget, ClassificationTimeout
registry = Registry('github.com/acme/project')
try:
    with Budget(0.03).activate():
        registry.update(lambda data: data.update(unexpected=True))
except ClassificationTimeout:
    print('deadline respected')
else:
    raise AssertionError('contended lock was acquired')
'''
    with lock_path.open('w') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        result = subprocess.run([sys.executable, '-c', script], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'deadline respected'
    assert 'unexpected' not in registry.load()


def test_incomplete_success_step_cannot_trigger_historical_discovery(registry, monkeypatch):
    api = FakeAPI()
    assert decide(api, registry)['result'] == 'known_flakes'
    original = actions.collect_snapshot
    def collect(*args):
        value = original(*args)
        value['checks'][0]['steps'].append({'number': 2, 'status': 'completed', 'conclusion': 'success',
            'report': {'complete': False, 'tests': [{'test': 'partial_test', 'signature': 'truncated',
                                                   'outcome': 'fail'}]}})
        return value
    monkeypatch.setattr(policy, 'collect_snapshot', collect)
    api.calls.clear()
    before = len(registry.load()['scans'])
    result = decide(api, registry)
    assert result['result'] == 'failure'
    assert any(b.get('code') == 'incomplete_named_test' for b in result['blockers'])
    assert not any(call[0] == 'issues' for call in api.calls)
    assert len(registry.load()['scans']) == before


@pytest.mark.parametrize('status', ['timed_out', 'completed'])
def test_scan_finalization_contention_is_bounded_and_retained(registry, status):
    # Run the complete lock/timeout interaction in a bounded child so a blocking
    # flock regression fails the test instead of hanging the test runner.
    driver = f'''
import fcntl, json, sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent)!r})
from test_flaky_tests import FakeAPI, REPO, snapshot
from shared import ci_policy as policy
from shared.ci_deadline import ClassificationTimeout
from shared.flaky_tests import Registry
registry = Registry(REPO)
tick = [0]
held = None
def discover(*args, **kwargs):
    global held
    lock = registry.path.parent / (registry.path.name + '.lock')
    held = lock.open('w')
    fcntl.flock(held, fcntl.LOCK_EX)
    tick[0] = 10
    if {status!r} == 'timed_out':
        raise ClassificationTimeout('discovery expired while another writer holds the lock')
    return {{'limits': {{}}, 'limits_reached': [], 'missing': [],
             'evidence_file': policy.evidence_file(registry, {{'discovery': 'complete'}})}}
policy.discover = discover
try:
    result = policy.decide(FakeAPI(body='unrelated'), registry, snapshot(), snapshot,
                           classification_timeout=5, clock=lambda: tick[0])
    print(json.dumps(result))
finally:
    if held:
        held.close()
'''
    child = subprocess.run([sys.executable, '-c', driver], capture_output=True, text=True, timeout=3)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['discovery']['status'] == status
    assert result['discovery']['registry_status'] == 'pending'
    assert 'scan_finalization' in result['discovery']['finalization_error']
    assert Path(result['evidence_file']).is_file() and Path(result['targets_file']).is_file()
    assert any(blocker.get('test') == TEST for blocker in result['blockers'])
    scan = next(iter(registry.load()['scans'].values()))
    assert scan['status'] == 'running'  # Failure is explicitly retained in the decision.
    assert 'targets' not in scan and scan['target_count'] == 1
    assert json.loads(Path(scan['targets_file']).read_text())['targets'][0]['test'] == TEST


def test_green_ci_does_not_refresh_unrelated_flake_issues(registry, monkeypatch):
    green = snapshot()
    green['result'] = 'success'
    green['checks'][0]['bucket'] = 'pass'
    def unrelated(*args):
        raise AssertionError('green CI must not depend on flake issue history')
    monkeypatch.setattr(registry, 'synchronize', unrelated)
    result = policy.decide(FakeAPI(), registry, green, lambda: copy.deepcopy(green))
    assert result['result'] == result['raw_result'] == 'success'
    assert result['verified_at'] is None
    assert not result['classification']['timed_out']


def test_request_timeout_with_remaining_budget_is_missing_evidence(registry):
    api = FakeAPI(body='unrelated')
    tick = [0]
    def timeout(limit):
        tick[0] = 60
        raise subprocess.TimeoutExpired('gh issue list', 60)
    api.open_issues = timeout
    result = decide(api, registry, classification_timeout=120, clock=lambda: tick[0])
    assert result['result'] == 'failure' and not result['classification']['timed_out']
    assert any('gh issue list' in detail and '60' in detail
               for detail in result['discovery']['missing'])
    assert all(scan['status'] != 'timed_out' for scan in registry.load()['scans'].values())


def test_final_read_timeout_is_distinct_from_classification_expiry(registry):
    tick = [0]
    def refresh():
        tick[0] = 60
        raise subprocess.TimeoutExpired('gh pr view', 60)
    result = policy.decide(FakeAPI(), registry, snapshot(), refresh,
                          classification_timeout=120, clock=lambda: tick[0])
    assert result['result'] == 'failure' and not result['classification']['timed_out']
    assert any('request timed out before' in blocker['reason'] for blocker in result['blockers'])


def test_interrupted_snapshot_retains_completed_check_sources(registry):
    api = FakeAPI()
    tick = [0]
    original = api.request
    def request(endpoint, binary=False):
        if endpoint == 'actions/runs/20':
            tick[0] = 10
            return dict(api.run, id=20)
        return original(endpoint, binary)
    api.request = request
    snap = snapshot()
    snap['checks'].append(dict(snap['checks'][0],
        link='https://github.com/acme/project/actions/runs/20/job/201'))
    result = policy.decide(api, registry, snap, lambda: copy.deepcopy(snap),
                          classification_timeout=5, clock=lambda: tick[0])
    assert result['result'] == 'failure' and result['classification']['timed_out']
    retained = json.loads(Path(result['evidence_file']).read_text())
    assert retained['checks'][0]['job_id'] == 101
    assert Path(retained['checks'][0]['source_file']).is_file()
    assert Path(retained['checks'][0]['archive_file']).is_file()
    assert 'deadline' in retained['checks'][1]['error']


def test_deferred_legacy_import_lock_wait_is_inside_classification_budget(registry):
    driver = f'''
import fcntl, json, sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent)!r})
from test_flaky_tests import FakeAPI, REPO, snapshot
from shared import ci_policy as policy
from shared.flaky_tests import Registry
from shared.private_state import root
legacy = root('GitHub.com/Acme/Project') / 'flaky-tests.json'
legacy.parent.mkdir(parents=True, exist_ok=True)
legacy.write_text(json.dumps({{'tests': {{'legacy': [{{'outcome': 'fail'}}]}}}}))
registry = Registry(REPO, import_legacy=False)
lock = registry.path.parent / (registry.path.name + '.lock')
lock.parent.mkdir(parents=True, exist_ok=True)
api = FakeAPI()
with lock.open('w') as held:
    fcntl.flock(held, fcntl.LOCK_EX)
    result = policy.decide(api, registry, snapshot(), snapshot, classification_timeout=0.03)
assert api.calls == []
assert Registry(REPO).load()['tests']['legacy'] == [{{'outcome': 'fail'}}]
assert json.loads(legacy.read_text())['tests']['legacy'] == [{{'outcome': 'fail'}}]
print(json.dumps(result))
'''
    child = subprocess.run([sys.executable, '-c', driver], capture_output=True, text=True, timeout=3)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['classification']['phase'] == 'initialize_registry'
    assert Path(result['decision_file']).is_file()


def test_per_call_deadline_checks_do_not_flood_phase_progress():
    tick = [0]
    updates = []
    budget = Budget(5, clock=lambda: tick[0], on_progress=updates.append)
    budget.progress('discover_unknown', target_count=1)
    api = BoundedAPI(FakeAPI(), budget)
    for _ in range(100):
        api.request('actions/runs/10')
    assert len(updates) == 1 and updates[0]['phase'] == 'discover_unknown'
    tick[0] = 5
    with pytest.raises(ClassificationTimeout):
        api.request('actions/runs/10')
    assert len(api.api.calls) == 100 and len(updates) == 1


def test_request_timeout_keeps_command_endpoint_and_duration():
    endpoint = 'actions/runs/10/attempts/2/logs'
    api = FakeAPI()
    def timeout(requested, binary=False):
        raise subprocess.TimeoutExpired(['gh', 'api', 'repos/acme/project/' + requested], 60)
    api.request = timeout
    bounded = BoundedAPI(api, Budget(120, clock=lambda: 0))
    with pytest.raises(RuntimeError) as failed:
        bounded.request(endpoint)
    assert endpoint in str(failed.value) and 'gh' in str(failed.value)
    assert '60' in str(failed.value)
