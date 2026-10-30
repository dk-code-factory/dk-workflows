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


def test_final_attempt_expiry_reports_live_verification_phase(registry):
    api = FakeAPI()
    tick, run_reads, updates = [0], [0], []
    original = api.request
    def request(endpoint, binary=False):
        if endpoint == 'actions/runs/10':
            run_reads[0] += 1
            if run_reads[0] == 2:
                tick[0] = 10
        return original(endpoint, binary)
    api.request = request
    def refresh():
        raise AssertionError('expired live attempt verification must stop further reads')
    result = policy.decide(api, registry, snapshot(), refresh,
                          classification_timeout=5, clock=lambda: tick[0],
                          on_progress=updates.append)
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['classification']['phase'] == updates[-1]['phase'] == 'verify_current'
    assert result['ignored_failures'] == []
    assert run_reads[0] == 2


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


def timeout_production_transport(monkeypatch):
    from shared import github_reads
    failures = []
    def run(argv, **kwargs):
        error = subprocess.TimeoutExpired(argv, kwargs['timeout'])
        failures.append(error)
        raise error
    monkeypatch.setattr(subprocess, 'run', run)
    monkeypatch.setattr(github_reads.time, 'sleep', lambda seconds: None)
    return failures


def test_production_request_timeout_keeps_original_transport_details(monkeypatch):
    failures = timeout_production_transport(monkeypatch)
    endpoint = 'actions/runs/10/attempts/2/logs'
    api = BoundedAPI(actions.GitHub(REPO), Budget(120))
    with pytest.raises(RuntimeError) as failed:
        api.request(endpoint)
    assert len(failures) == 3
    assert str(failures[-1]) in str(failed.value)
    assert endpoint in str(failed.value)


def test_production_issue_timeout_survives_registry_and_decision(registry, monkeypatch):
    from test_flaky_tests import URL
    fake = FakeAPI()
    assert decide(fake, registry)['result'] == 'known_flakes'
    evidence = actions.collect_snapshot(fake, registry, snapshot())
    failures = timeout_production_transport(monkeypatch)
    api = BoundedAPI(actions.GitHub(REPO), Budget(120))
    verification = registry.synchronize([], api.issue)
    error = verification['issue_errors'][URL]
    assert str(failures[-1]) in error and 'issues/7' in error
    assert verification['unavailable_issues'] == [URL]
    saved = registry.load()['issue_failures'][URL][-1]
    assert saved['error'] == error
    decision = policy.evaluate(snapshot(), evidence, verification, REPO)
    assert decision['result'] == 'failure' and decision['issue_errors'][URL] == error
    assert not decision['ignored_failures']


def test_prior_issue_error_survives_later_fetch_expiry_and_serialization(registry):
    from test_flaky_tests import URL
    api = FakeAPI()
    assert decide(api, registry)['result'] == 'known_flakes'
    other_url = URL.rsplit('/', 1)[0] + '/8'
    other = dict(api.issue_data, number=8, html_url=other_url)
    registry.synchronize([api.issue_data, other],
        lambda number: api.issue_data if number == 7 else other)
    tick = [0]
    message = "Command 'gh api repos/acme/project/issues/7' timed out after 0.05 seconds"
    def issue(number):
        if number == 7:
            raise RuntimeError(message)
        tick[0] = 10
        return other
    api.issue = issue
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert result['issue_errors'][URL] == message
    assert json.loads(Path(result['decision_file']).read_text())['issue_errors'][URL] == message
    assert not result['ignored_failures']


def test_prior_issue_error_survives_synchronization_lock_expiry(registry):
    driver = f'''
import fcntl, json, sys
from pathlib import Path
sys.path.insert(0, {str(Path(__file__).parent)!r})
from test_flaky_tests import FakeAPI, REPO, URL, snapshot
from shared import ci_policy as policy
from shared.flaky_tests import Registry
registry = Registry(REPO)
api = FakeAPI()
assert policy.decide(api, registry, snapshot(), snapshot)['result'] == 'known_flakes'
held = []
message = "Command 'gh api repos/acme/project/issues/7' timed out after 0.05 seconds"
def issue(number):
    lock = registry.path.parent / (registry.path.name + '.lock')
    stream = lock.open('w')
    fcntl.flock(stream, fcntl.LOCK_EX)
    held.append(stream)
    raise RuntimeError(message)
api.issue = issue
try:
    result = policy.decide(api, registry, snapshot(), snapshot, classification_timeout=0.5)
    assert held, 'classification expired before the issue-error/lock interaction'
    stored = json.loads(Path(result['decision_file']).read_text())
    assert result['issue_errors'][URL] == stored['issue_errors'][URL] == message
    print(json.dumps(result))
finally:
    for stream in held:
        stream.close()
'''
    child = subprocess.run([sys.executable, '-c', driver], capture_output=True, text=True, timeout=3)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result['result'] == 'failure' and result['classification']['timed_out']
    assert not result['ignored_failures']


def test_targeted_discovery_finds_history_beyond_busy_unrelated_workflows(registry):
    api = FakeAPI(body=declaration(False), attempts=2)
    api.texts[2] = PASS
    original = api.request
    def request(endpoint, binary=False):
        if endpoint.startswith('actions/runs?'):
            return {'total_count': 101, 'workflow_runs': [
                dict(api.run, id=1000 + i, path='.github/workflows/unrelated.yml')
                for i in range(100)]}
        if endpoint.startswith('actions/workflows/ci.yml/runs?'):
            api.calls.append(('request', endpoint))
            return {'total_count': 1, 'workflow_runs': [copy.deepcopy(api.run)]}
        return original(endpoint, binary)
    api.request = request
    report = actions.discover(api, registry, targets=[
        dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)])
    assert report['attempts'] == 2 and report['runs'] == 1
    assert report['verification']['entries'][0]['active']


def test_report_parsing_checks_deadline_and_retains_downloaded_sources(registry, monkeypatch):
    from shared import test_evidence
    events = [{'Action': 'run', 'Package': 'p', 'Test': f'Test{i}'} for i in range(1000)]
    api = FakeAPI(text='\n'.join(map(json.dumps, events)))
    tick = [0]
    original = test_evidence.json.loads
    reads = []
    def loads(text, *args, **kwargs):
        if isinstance(text, str) and text.startswith('{"Action"') and '\n' not in text:
            reads.append(text)
            if len(reads) == 10:
                tick[0] = 10
        return original(text, *args, **kwargs)
    monkeypatch.setattr(test_evidence.json, 'loads', loads)
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out'] and result['result'] == 'failure'
    assert len(reads) < 100
    snapshot_evidence = json.loads(Path(result['evidence_file']).read_text())
    attempt = json.loads(Path(snapshot_evidence['checks'][0]['attempt_evidence_file']).read_text())
    assert Path(attempt['archive_file']).is_file() and Path(attempt['source_file']).is_file()


def test_large_go_report_groups_events_without_repeated_scans():
    from shared import test_evidence
    # Counting equality calls detects rescanning every event for every test.
    class Package(str):
        comparisons = 0
        def __eq__(self, other):
            Package.comparisons += 1
            return super().__eq__(other)
        __hash__ = str.__hash__
    count = 1000
    events = [dict(Action=action, Package=Package('p'), Test=f'Test{i}')
              for i in range(count) for action in ('run', 'pass')]
    events.append(dict(Action='pass', Package=Package('p')))
    from unittest.mock import patch
    with patch.object(test_evidence.json, 'loads', side_effect=events):
        report = test_evidence.go_output('\n'.join(['{}'] * len(events)))
    assert report['complete'] and len(report['tests']) == count
    assert Package.comparisons < count * 20


def test_targeted_workflow_queries_share_one_run_limit(registry, monkeypatch):
    api = FakeAPI(body='unrelated')
    queries, collected = [], []
    original = api.request
    def request(endpoint, binary=False):
        if endpoint.startswith('actions/workflows/'):
            queries.append(endpoint)
            workflow = '.github/workflows/' + endpoint.split('/')[2]
            return {'total_count': 1, 'workflow_runs': [dict(api.run, path=workflow)]}
        return original(endpoint, binary)
    api.request = request
    def collect(api, registry, run, attempt, **kwargs):
        collected.append(run['path'])
        return {'missing': []}
    monkeypatch.setattr(actions, 'collect_attempt', collect)
    targets = [dict(workflow=f'.github/workflows/{name}.yml', job=JOB, test=TEST, signature=SIGNATURE)
               for name in ('one', 'two', 'three')]
    report = actions.discover(api, registry, max_runs=2, targets=targets)
    assert len(queries) == len(collected) == report['runs'] == 2
    assert 'per_page=2' in queries[0] and 'per_page=1' in queries[1]
    assert 'runs' in report['limits_reached']


def test_historical_parse_timeout_keeps_both_current_and_attempt_evidence(registry, monkeypatch):
    from shared import test_evidence
    api = FakeAPI(body=declaration(False))
    original = actions.archive_report
    tick, parses = [0], []
    def parse(*args):
        parses.append(args)
        if len(parses) == 2:
            tick[0] = 10
            test_evidence.parse_report('')
        return original(*args)
    monkeypatch.setattr(actions, 'archive_report', parse)
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out'] and result['result'] == 'failure'
    current = json.loads(Path(result['evidence_file']).read_text())
    historical = json.loads(Path(result['discovery']['evidence_file']).read_text())
    assert current['checks'][0]['job_id'] == 101
    assert Path(historical['archive_file']).is_file() and historical['missing']


@pytest.mark.parametrize('historical', [False, True])
def test_log_download_expiry_retains_attempt_provenance(registry, historical):
    api = FakeAPI(body=declaration(False))
    original = api.request
    tick, downloads = [0], [0]
    def request(endpoint, binary=False):
        if endpoint.endswith('/logs'):
            downloads[0] += 1
            if downloads[0] == (2 if historical else 1):
                tick[0] = 10
        return original(endpoint, binary)
    api.request = request
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out'] and result['result'] == 'failure'
    current = json.loads(Path(result['evidence_file']).read_text())
    attempt_path = (result['discovery']['evidence_file'] if historical else
                    current['checks'][0]['attempt_evidence_file'])
    attempt = json.loads(Path(attempt_path).read_text())
    source = json.loads(Path(attempt['source_file']).read_text())
    assert source['run']['id'] == attempt['run_id'] == 10
    assert source['jobs'][0]['id'] == 101
    assert any('deadline' in missing for missing in attempt['missing'])
    assert Path(json.loads(Path(result['decision_file']).read_text())['evidence_file']).is_file()


def test_large_issue_matching_respects_classification_deadline(registry, monkeypatch):
    from shared import flaky_tests
    text = '\n'.join(f'FAILED test_{i} - error' for i in range(200))
    api = FakeAPI(text=text + '\n================ 200 failed in 1.00s ================', body='unrelated')
    api.open_issues = lambda limit: [copy.deepcopy(api.issue_data) for _ in range(100)]
    original = flaky_tests.issue_match
    tick, matches = [0], []
    def match(*args):
        matches.append(args)
        if len(matches) == 10:
            tick[0] = 10
        return original(*args)
    monkeypatch.setattr(flaky_tests, 'issue_match', match)
    result = decide(api, registry, classification_timeout=5, clock=lambda: tick[0])
    assert result['classification']['timed_out'] and result['result'] == 'failure'
    assert result['classification']['phase'] == 'discover_unknown'
    assert len(matches) <= 10


def test_historical_proof_checks_deadline_while_scanning_observations():
    from shared.flaky_tests import historical_proof
    target = dict(workflow=WORKFLOW, job=JOB, test=TEST, signature=SIGNATURE)
    observations = [target | {'complete': True, 'outcome': 'fail'} for _ in range(1000)]
    tick = [0]
    def clock():
        tick[0] += 1
        return tick[0]
    with Budget(5, clock=clock).activate(), pytest.raises(ClassificationTimeout):
        historical_proof(observations, target)


@pytest.mark.parametrize('field,value', [('Package', []), ('Package', 1), ('Action', []),
                                        ('Test', ['TestOne']), ('Test', 1),
                                        ('Output', 7), ('Output', None)])
def test_malformed_go_field_types_return_blockers_without_discovery(registry, field, value):
    events = [dict(Action='output', Package='p', Test='TestOne', Output='late'),
              dict(Action='fail', Package='p', Test='TestOne'), dict(Action='fail', Package='p')]
    events[0][field] = value
    api = FakeAPI(text='\n'.join(map(json.dumps, events)))
    result = decide(api, registry)
    assert result['result'] == result['raw_result'] == 'failure'
    assert result['blockers'] and not result['ignored_failures']
    assert not any(call[0] == 'issues' for call in api.calls)
    assert not registry.load()['scans']
    assert Path(result['evidence_file']).is_file() and Path(result['decision_file']).is_file()


def test_nested_junit_suite_counts_use_bounded_traversal(monkeypatch):
    from shared import test_evidence
    visits = [0]
    class CountingElement(test_evidence.ET.Element):
        def iter(self, tag=None):
            for element in super().iter(tag):
                visits[0] += 1
                yield element
    count = 1000
    text = ''.join(f'<testsuite tests="{count - i}"><testcase name="test_{i}"/>'
                   for i in range(count)) + '</testsuite>' * count
    tree = test_evidence.ET.fromstring(text, parser=test_evidence.ET.XMLParser(
        target=test_evidence.ET.TreeBuilder(element_factory=CountingElement)))
    monkeypatch.setattr(test_evidence.ET, 'fromstring', lambda text: tree)
    report = test_evidence.parse_report(text)
    assert report['complete'] and len(report['tests']) == count
    assert visits[0] < count * 10, 'nested suites repeatedly traverse the same descendant cases'


def test_wide_junit_counts_cooperate_with_classification_deadline(monkeypatch):
    from shared import test_evidence
    tick, visits = [0], [0]
    # ElementTree's Element has sequence iteration rather than __iter__.
    class AdvancingChildren(test_evidence.ET.Element):
        def __iter__(self):
            for index in range(len(self)):
                visits[0] += 1
                if visits[0] == 10:
                    tick[0] = 10
                yield self[index]
    count = 1000
    text = f'<testsuite tests="{count}">' + ''.join(
        f'<testcase name="test_{i}"/>' for i in range(count)) + '</testsuite>'
    tree = test_evidence.ET.fromstring(text, parser=test_evidence.ET.XMLParser(
        target=test_evidence.ET.TreeBuilder(element_factory=AdvancingChildren)))
    monkeypatch.setattr(test_evidence.ET, 'fromstring', lambda text: tree)
    with Budget(5, clock=lambda: tick[0]).activate(), pytest.raises(ClassificationTimeout):
        test_evidence.parse_report(text)
    assert visits[0] < 100
