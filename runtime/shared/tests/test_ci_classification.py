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
